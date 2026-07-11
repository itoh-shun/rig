"""validation drill: /rig:drill coverage check for gate-bearing recipes (#266).

`/rig:drill` measures reviewer detection rates by injecting bug seeds from the
seed catalog (facets/instructions/drill.md, the "種の class" table) into a
synthetic diff and running review fan-out. A recipe's review/acceptance gate is
therefore only *drill-coverable* when the gate is enforced by reviewer personas
that have a corresponding seed class ("検出すべき観点" column) in that catalog.

This check WARNs (never FAILs — coverage guidance, not schema) for shipped
gate-bearing recipes that /rig:drill cannot exercise:
  - recipes whose reviewer personas all lack a seed class in the catalog, and
  - recipes with a gate but no reviewer personas at all (aggregated into one
    WARN; their gate efficacy is only visible via `rig stats` rubber-stamp
    detection, not via drill).
Recipes with at least one covered reviewer count as coverable, but reviewers
without a seed class are still surfaced (detection rate unmeasured for them).
"""

import pathlib

from .config import FACETS
from .state import _emit, parse_frontmatter

# Same gate values validation/recipes.py accepts (_VALID_GATES).
_GATE_VALUES = ("review-gate", "acceptance-gate", "magi-consensus")

# The seed catalog table is anchored by this header cell (perspective column).
_PERSPECTIVE_HEADER = "検出すべき観点"


def parse_seed_perspectives(drill_md: pathlib.Path) -> set[str]:
    """Extract the perspectives /rig:drill can exercise from the seed catalog.

    Reads the markdown table in facets/instructions/drill.md whose header row
    contains the "検出すべき観点" column and collects that column's tokens
    (cells like "design / lazy-senior" are split on "/"). Returns an empty set
    when the file or table is missing so the caller can WARN instead of crash.
    """
    if not drill_md.exists():
        return set()
    perspectives: set[str] = set()
    column: int | None = None
    for line in drill_md.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            column = None
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if _PERSPECTIVE_HEADER in cells:
            column = cells.index(_PERSPECTIVE_HEADER)
            continue
        if column is None or column >= len(cells):
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue  # separator row (|---|---|…)
        for token in cells[column].split("/"):
            token = token.strip()
            if token:
                perspectives.add(token)
    return perspectives


def _base_name(persona: str) -> str:
    """Last path segment of a persona reference (sales/hearing-reviewer → hearing-reviewer)."""
    return persona.rsplit("/", 1)[-1].strip()


def _is_reviewer(persona: str, perspectives: set[str]) -> bool:
    base = _base_name(persona)
    return base.endswith("-reviewer") or base in perspectives


def _is_covered(persona: str, perspectives: set[str]) -> bool:
    base = _base_name(persona)
    return base in perspectives or base.removesuffix("-reviewer") in perspectives


def check_drill_coverage(
    recipe_files: list[pathlib.Path],
    drill_instruction: pathlib.Path | None = None,
) -> None:
    """WARN for gate-bearing recipes that /rig:drill cannot exercise (#266)."""
    drill_md = drill_instruction or (FACETS / "instructions" / "drill.md")
    perspectives = parse_seed_perspectives(drill_md)
    if not perspectives:
        _emit(
            "WARN",
            f"drill coverage — seed catalog table ('{_PERSPECTIVE_HEADER}' column) not found in"
            f" {drill_md.name} (cannot check which gates /rig:drill exercises)",
        )
        return

    gated_total = 0
    coverable: list[str] = []
    no_reviewer: list[str] = []
    for path in recipe_files:
        fm, _ = parse_frontmatter(path)
        steps = (fm or {}).get("steps")
        if not isinstance(steps, list):
            continue
        steps = [s for s in steps if isinstance(s, dict)]
        if not any(s.get("gate") in _GATE_VALUES for s in steps):
            continue  # gate-less recipes are out of drill's scope
        gated_total += 1

        reviewers: list[str] = []
        for step in steps:
            for persona in step.get("personas") or []:
                if (isinstance(persona, str) and _is_reviewer(persona, perspectives)
                        and persona not in reviewers):
                    reviewers.append(persona)

        if not reviewers:
            no_reviewer.append(path.stem)
            continue

        uncovered = [p for p in reviewers if not _is_covered(p, perspectives)]
        if len(uncovered) == len(reviewers):
            _emit(
                "WARN",
                f"drill coverage — recipe {path.stem}: gate-bearing, but none of its reviewers"
                f" ({', '.join(reviewers)}) have a seed class in the drill catalog"
                f" (/rig:drill cannot exercise this gate; extend the seed catalog or the personas)",
            )
            continue
        if uncovered:
            _emit(
                "WARN",
                f"drill coverage — recipe {path.stem}: reviewers without a drill seed class:"
                f" {', '.join(uncovered)} (their detection rate stays unmeasured)",
            )
        coverable.append(path.stem)

    if no_reviewer:
        _emit(
            "WARN",
            "drill coverage — gate-bearing recipes with no reviewer personas"
            " (/rig:drill cannot exercise their gates; efficacy is only visible via"
            " `rig stats` rubber-stamp detection): " + ", ".join(sorted(no_reviewer)),
        )

    _emit(
        "PASS",
        f"drill coverage: {len(coverable)}/{gated_total} gate-bearing recipes exercisable by"
        f" /rig:drill (seed perspectives: {', '.join(sorted(perspectives))})",
    )
